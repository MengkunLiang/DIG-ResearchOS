"""Deterministic population mechanics for the T4 evolution controller.

The functions here never make a scientific claim or write candidate prose. They
operate on already-authored typed artifacts to preserve lineage, enforce
contracts, and make quality/diversity decisions reproducible.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import re
from typing import Iterable

from .models import (
    CandidateDossier,
    ComplexityReport,
    CrossoverCompatibilityDecision,
    EvolutionOperator,
    EvolutionPlan,
    GeneDelta,
    GeneDonorMap,
    IdeaContractResult,
    IdeaFamily,
    IdeaGenome,
    PopulationSnapshot,
    PortfolioSelection,
    ScoreReport,
)
from .state import stable_fingerprint


GENE_NAMES = (
    "problem",
    "opportunity",
    "challenged_assumption",
    "core_thesis",
    "mechanism",
    "design_or_artifact",
    "contribution_package",
    "hypothesis_bundle",
    "validation_logic",
    "boundary_conditions",
    "risks",
)


def build_idea_families(
    genomes: list[IdeaGenome],
    *,
    generation: int,
    similarity_threshold: float,
) -> list[IdeaFamily]:
    """Build complete-link families without dropping any candidate."""

    unassigned = list(genomes)
    families: list[list[IdeaGenome]] = []
    while unassigned:
        seed = unassigned.pop(0)
        family = [seed]
        remainder: list[IdeaGenome] = []
        for candidate in unassigned:
            if all(genome_similarity(candidate, member) >= similarity_threshold for member in family):
                family.append(candidate)
            else:
                remainder.append(candidate)
        families.append(family)
        unassigned = remainder

    result: list[IdeaFamily] = []
    for index, members in enumerate(families, start=1):
        representative = members[0]
        result.append(
            IdeaFamily(
                family_id=f"F{generation}-{index}",
                generation=generation,
                family_title=_text(representative.problem.value)[:120],
                shared_problem=_text(representative.problem.value),
                member_ids=[member.candidate_id for member in members],
                champion_id=None,
                sibling_family_ids=[],
                gene_donors={},
            )
        )
    for left in result:
        for right in result:
            if left.family_id == right.family_id:
                continue
            left_problem = _tokens(left.shared_problem)
            right_problem = _tokens(right.shared_problem)
            if _jaccard(left_problem, right_problem) >= similarity_threshold:
                left.sibling_family_ids.append(right.family_id)
    return result


def genome_similarity(left: IdeaGenome, right: IdeaGenome) -> float:
    """Three-axis structural similarity, not a novelty calculation."""

    problem = _jaccard(_tokens(_text(left.problem.value)), _tokens(_text(right.problem.value)))
    mechanism = _jaccard(_tokens(_text(left.mechanism.value)), _tokens(_text(right.mechanism.value)))
    contribution = _jaccard(
        _tokens(_text(left.contribution_package.value)),
        _tokens(_text(right.contribution_package.value)),
    )
    return round((problem + mechanism + contribution) / 3, 6)


def select_evolution_parents(
    dossiers: list[CandidateDossier],
    scores: list[ScoreReport],
    families: list[IdeaFamily],
    *,
    maximum: int,
    profile_weight: float = 0.0,
) -> tuple[list[str], dict[str, str]]:
    """Choose Pareto-visible, family-diverse Parents without diagnostic scoring.

    ``profile_weight`` is retained as an API compatibility parameter. Target
    Profile remains valuable context for the LLM and the Human Gate, but it is
    not a fourth score or an automatic parent-selection multiplier.
    """

    del profile_weight
    score_by_id = {report.candidate_id: report for report in scores}
    dossier_by_id = {item.candidate_id: item for item in dossiers}
    selected: list[str] = []
    reasons: dict[str, str] = {}
    available = [candidate_id for candidate_id in dossier_by_id if candidate_id in score_by_id]
    pareto_layers = _pareto_layers(available, score_by_id)

    # First make non-dominated Candidates visible. This avoids a scalar
    # ``overall`` score hiding a Candidate that is especially strong on one of
    # the three formal scientific dimensions.
    for candidate_id in _flatten_layers(pareto_layers):
        if len(selected) >= maximum:
            break
        family = _family_for_candidate(candidate_id, families)
        if any(_family_for_candidate(existing, families) == family for existing in selected):
            continue
        selected.append(candidate_id)
        report = score_by_id[candidate_id]
        reasons[candidate_id] = "wildcard" if report.wildcard_recommended else "pareto_family_representative"

    # Then ensure every Family with a scored Candidate can contribute one
    # Parent before using a scalar tie-break. This is a diversity scheduling
    # choice, not a scientific judgement about the Family.
    for family in families:
        ranked = sorted(
            (candidate_id for candidate_id in family.member_ids if candidate_id in score_by_id and candidate_id in dossier_by_id),
            key=lambda candidate_id: _parent_priority(score_by_id[candidate_id]),
            reverse=True,
        )
        if not ranked or len(selected) >= maximum:
            continue
        if any(candidate_id in selected for candidate_id in ranked):
            continue
        choice = ranked[0]
        selected.append(choice)
        score = score_by_id[choice]
        reasons[choice] = "wildcard" if score.wildcard_recommended else "family_representative"
    for candidate_id, report in sorted(
        score_by_id.items(),
        key=lambda item: _parent_priority(item[1]),
        reverse=True,
    ):
        if len(selected) >= maximum:
            break
        if candidate_id in selected or candidate_id not in dossier_by_id:
            continue
        selected.append(candidate_id)
        reasons[candidate_id] = "wildcard" if report.wildcard_recommended else "core_score_representative"
    return selected, reasons


def compile_mutation_plans(
    parent_ids: list[str],
    scores: list[ScoreReport],
    *,
    round_number: int,
    limit: int,
) -> list[EvolutionPlan]:
    score_by_id = {item.candidate_id: item for item in scores}
    plans: list[EvolutionPlan] = []
    for index, parent_id in enumerate(parent_ids[:limit], start=1):
        report = score_by_id[parent_id]
        operator = report.recommended_operators[0] if report.recommended_operators else EvolutionOperator.REPAIR_VALIDATION
        payload = {
            "round": round_number,
            "type": "mutation",
            "parent": parent_id,
            "operator": operator.value,
            "preserve": report.preserve_genes,
            "modify": report.modify_genes,
        }
        plans.append(
            EvolutionPlan(
                plan_id=f"EP{round_number}-M{index}",
                plan_fingerprint=stable_fingerprint(payload),
                round=round_number,
                child_type="mutation",
                parent_ids=[parent_id],
                operator=operator,
                preserve_genes=report.preserve_genes,
                modify_genes=report.modify_genes,
                constraints=[report.dominant_bottleneck],
                expected_improvements=["Address the independently scored dominant bottleneck."],
                failure_conditions=["The preserved genes materially regress or evidence permission is elevated."],
            )
        )
    return plans


def compile_crossover_plans(
    decisions: list[CrossoverCompatibilityDecision],
    *,
    round_number: int,
    limit: int,
) -> list[EvolutionPlan]:
    plans: list[EvolutionPlan] = []
    for index, decision in enumerate((item for item in decisions if item.decision == "approved") , start=1):
        if len(plans) >= limit or decision.proposed_gene_donor_map is None:
            break
        payload = {
            "round": round_number,
            "type": "crossover",
            "parents": decision.parent_ids,
            "donors": decision.proposed_gene_donor_map.donors,
        }
        plans.append(
            EvolutionPlan(
                plan_id=f"EP{round_number}-C{index}",
                plan_fingerprint=stable_fingerprint(payload),
                round=round_number,
                child_type="crossover",
                parent_ids=decision.parent_ids,
                operator=EvolutionOperator.CROSSOVER,
                gene_donor_map=decision.proposed_gene_donor_map,
                constraints=decision.conflicts,
                expected_improvements=[decision.bottleneck_complementarity],
                failure_conditions=["A single coherent mechanism cannot be stated."],
            )
        )
    return plans


def compute_gene_delta(child: CandidateDossier, parents: list[CandidateDossier], plan: EvolutionPlan) -> GeneDelta:
    parent_genomes = [parent.genome for parent in parents if parent.candidate_id in plan.parent_ids]
    if not parent_genomes:
        raise ValueError("plan parents are not available")
    changed: list[str] = []
    preserved: list[str] = []
    violations: list[str] = []
    baseline = parent_genomes[0]
    for name in GENE_NAMES:
        child_value = _text(getattr(child.genome, name).value)
        parent_values = {_text(getattr(parent, name).value) for parent in parent_genomes}
        if child_value in parent_values:
            preserved.append(name)
        else:
            changed.append(name)
        if name in plan.preserve_genes and child_value not in parent_values:
            violations.append(name)
    parent_words = max(1, len(_genome_text(baseline).split()))
    ratio = len(_genome_text(child.genome).split()) / parent_words
    if violations:
        classification = "regressive"
    elif not changed:
        classification = "cosmetic"
    elif len(changed) == 1 and ratio <= 1.1:
        classification = "clarification_only"
    else:
        classification = "substantive"
    return GeneDelta(
        child_id=child.candidate_id,
        parent_ids=plan.parent_ids,
        classification=classification,
        changed_genes=changed,
        preserved_genes=preserved,
        violated_preserve_constraints=violations,
        word_count_growth_ratio=round(ratio, 4),
    )


def detect_complexity_inflation(child: CandidateDossier, parents: list[CandidateDossier], *, ratio_limit: float) -> ComplexityReport:
    parent_words = max(1, max(len(_genome_text(parent.genome).split()) for parent in parents))
    ratio = len(_genome_text(child.genome).split()) / parent_words
    components = _new_list_items(child.genome.design_or_artifact.value, [parent.genome.design_or_artifact.value for parent in parents])
    data_requirements = _new_list_items(child.genome.validation_logic.value, [parent.genome.validation_logic.value for parent in parents])
    stages = _new_list_items(child.genome.contribution_package.value, [parent.genome.contribution_package.value for parent in parents])
    growth = "high" if ratio > ratio_limit else "medium" if ratio > 1.2 else "low"
    return ComplexityReport(
        candidate_id=child.candidate_id,
        complexity_growth=growth,
        new_components=components,
        new_data_requirements=data_requirements,
        new_experiment_stages=stages,
        expected_gain="Requires independent union scoring.",
        decision_hint="review_complexity" if growth == "high" else "acceptable",
    )


def validate_idea_contract(dossier: CandidateDossier) -> IdeaContractResult:
    """Validate structural maturity without evaluating academic merit."""

    contracts: dict[str, str] = {}
    hard_failures: list[str] = []
    warnings: list[str] = []
    for label, gene in (
        ("opportunity", dossier.genome.opportunity),
        ("thesis", dossier.genome.core_thesis),
        ("mechanism", dossier.genome.mechanism),
        ("contribution", dossier.genome.contribution_package),
        ("validation", dossier.genome.validation_logic),
        ("boundary", dossier.genome.boundary_conditions),
    ):
        contracts[label] = "pass" if _text(gene.value) else "fail"
        if contracts[label] == "fail":
            hard_failures.append(f"missing_{label}")
    contracts["hypothesis"] = "pass" if len(dossier.hypotheses) >= 2 else "warning"
    if contracts["hypothesis"] == "warning":
        warnings.append("Mature candidates require at least two one-line hypotheses.")
    contracts["evidence"] = "pass"
    for gene_name in GENE_NAMES:
        provenance = getattr(dossier.genome, gene_name).provenance
        if provenance.evidence_role.value in {"anchor", "support"} and not provenance.source_refs:
            contracts["evidence"] = "fail"
            hard_failures.append(f"missing_source_ref:{gene_name}")
    status = "fail" if hard_failures else "pass_with_warning" if warnings else "pass"
    return IdeaContractResult(
        candidate_id=dossier.candidate_id,
        status=status,
        contracts=contracts,
        hard_failures=hard_failures,
        warnings=warnings,
    )


def select_survivors(
    dossiers: list[CandidateDossier],
    scores: list[ScoreReport],
    contracts: list[IdeaContractResult],
    deltas: list[GeneDelta],
    complexity_reports: list[ComplexityReport],
    families: list[IdeaFamily],
    *,
    target_size: int,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Perform object isolation then Pareto/diversity survival.

    Only runtime-invalid objects are excluded here. Evidence calibration,
    validation feasibility, Profile Fit, uncertainty, and scientific upside
    are retained as diagnostics; none can invalidate a Candidate or become a
    hidden numerical survival dimension.
    """

    score_by_id = {item.candidate_id: item for item in scores}
    contract_by_id = {item.candidate_id: item for item in contracts}
    delta_by_id = {item.child_id: item for item in deltas}
    viable = [
        item.candidate_id for item in dossiers
        if item.candidate_id in score_by_id
        and contract_by_id.get(item.candidate_id, IdeaContractResult(candidate_id=item.candidate_id, status="fail", contracts={})).status != "fail"
        and delta_by_id.get(item.candidate_id, GeneDelta(child_id=item.candidate_id, parent_ids=[item.candidate_id], classification="substantive", word_count_growth_ratio=1)).classification not in {"cosmetic", "regressive"}
    ]
    selected: list[str] = []
    decisions: dict[str, str] = {}
    family_counts: dict[str, int] = defaultdict(int)
    dossier_by_id = {item.candidate_id: item for item in dossiers}
    scoreable = [candidate_id for candidate_id in viable if candidate_id in score_by_id]
    layers = _pareto_layers(scoreable, score_by_id)

    # First pass applies a soft Family cap. A Population with too few Families
    # is completed below rather than manufactured or globally rejected.
    for layer_index, layer in enumerate(layers):
        remaining = set(layer)
        while remaining and len(selected) < target_size:
            eligible = [
                candidate_id
                for candidate_id in remaining
                if family_counts[_family_for_candidate(candidate_id, families)] < 2
            ]
            if not eligible:
                break
            choice = max(
                eligible,
                key=lambda candidate_id: _survival_choice_priority(
                    candidate_id,
                    score_by_id=score_by_id,
                    dossiers_by_id=dossier_by_id,
                    selected_ids=selected,
                    delta_by_id=delta_by_id,
                ),
            )
            remaining.remove(choice)
            selected.append(choice)
            family_counts[_family_for_candidate(choice, families)] += 1
            decisions[choice] = "family_survivor" if layer_index == 0 and family_counts[_family_for_candidate(choice, families)] == 1 else "quality_diversity_survivor"

    # The cap is intentionally soft: retain real Candidates when the current
    # scientific space genuinely contains fewer than two Families.
    if len(selected) < target_size:
        for layer in layers:
            for candidate_id in sorted(
                (item for item in layer if item not in selected),
                key=lambda item: _survival_choice_priority(
                    item,
                    score_by_id=score_by_id,
                    dossiers_by_id=dossier_by_id,
                    selected_ids=selected,
                    delta_by_id=delta_by_id,
                ),
                reverse=True,
            ):
                if len(selected) >= target_size:
                    break
                selected.append(candidate_id)
                family_counts[_family_for_candidate(candidate_id, families)] += 1
                decisions[candidate_id] = "quality_diversity_survivor"

    # Preserve at most one explicitly requested Wildcard if ordinary Pareto
    # and Family selection would hide it. This is a qualitative Human/LLM
    # signal, never a manufactured numerical boost.
    wildcards = [candidate_id for candidate_id in scoreable if score_by_id[candidate_id].wildcard_recommended]
    if wildcards and not any(candidate_id in selected for candidate_id in wildcards):
        wildcard = max(wildcards, key=lambda candidate_id: _core_mean(score_by_id[candidate_id]))
        if len(selected) < target_size:
            selected.append(wildcard)
            decisions[wildcard] = "wildcard_preserved"
        elif selected:
            replaceable = [candidate_id for candidate_id in selected if not score_by_id[candidate_id].wildcard_recommended]
            if replaceable:
                displaced = min(replaceable, key=lambda candidate_id: _core_mean(score_by_id[candidate_id]))
                selected[selected.index(displaced)] = wildcard
                decisions[wildcard] = "wildcard_preserved"
                decisions[displaced] = "archived_after_wildcard_preservation"
    # A provider outage does not make a scientifically valid Parent disappear.
    # Unscored candidates are appended deterministically after independently
    # scored survivors. They are visible as unranked and never beat a scored
    # candidate merely because a synthetic fallback score was assigned.
    unscored_viable = [
        item.candidate_id
        for item in dossiers
        if item.candidate_id not in score_by_id
        and contract_by_id.get(
            item.candidate_id,
            IdeaContractResult(candidate_id=item.candidate_id, status="fail", contracts={}),
        ).status != "fail"
    ]
    for candidate_id in unscored_viable:
        if len(selected) >= target_size:
            break
        if candidate_id not in selected:
            selected.append(candidate_id)
            decisions[candidate_id] = "unscored_retained_for_review"
    archived = [item.candidate_id for item in dossiers if item.candidate_id not in selected]
    for candidate_id in archived:
        decisions[candidate_id] = "archived_after_survival"
    return selected, archived, decisions


def select_portfolio(
    population: PopulationSnapshot,
    scores: list[ScoreReport],
    families: list[IdeaFamily],
    *,
    maximum: int,
    profile_weight: float = 0.0,
) -> PortfolioSelection:
    score_by_id = {item.candidate_id: item for item in scores}
    candidate_families = {candidate_id: family.family_id for family in families for candidate_id in family.member_ids}
    # Publication Orientation controls narrative focus and a separately
    # displayed Profile Fit. It must not convert a venue preference into a
    # hidden score multiplier for the scientific Portfolio.
    del profile_weight
    ranked_scored = sorted(
        [candidate_id for candidate_id in population.active_candidate_ids if candidate_id in score_by_id],
        key=lambda candidate_id: _portfolio_priority(score_by_id[candidate_id]),
        reverse=True,
    )
    ranked = ranked_scored + [
        candidate_id for candidate_id in population.active_candidate_ids if candidate_id not in score_by_id
    ]
    chosen: list[str] = []
    families_seen: set[str] = set()
    for candidate_id in ranked:
        family_id = candidate_families.get(candidate_id, candidate_id)
        if family_id in families_seen and len(chosen) < maximum:
            continue
        chosen.append(candidate_id)
        families_seen.add(family_id)
        if len(chosen) == maximum:
            break
    # Reserve space for an explicitly LLM-identified high-upside Wildcard when
    # the ordinary readiness ranking would otherwise hide it.  This is not a
    # fabricated score and it does not make the Candidate selection-ready; it
    # merely keeps a conjectural research programme visible to the human.
    wildcard_ids = [
        candidate_id
        for candidate_id in ranked_scored
        if score_by_id[candidate_id].wildcard_recommended or score_by_id[candidate_id].high_upside
    ]
    if maximum > 1 and wildcard_ids and not any(candidate_id in wildcard_ids for candidate_id in chosen):
        wildcard = wildcard_ids[0]
        if len(chosen) < maximum:
            chosen.append(wildcard)
        elif chosen:
            chosen[-1] = wildcard
    if not chosen and ranked:
        chosen.append(ranked[0])
    lead = chosen[0] if chosen else None
    high_upside = [
        item.candidate_id
        for item in scores
        if (item.high_upside or item.wildcard_recommended)
        and item.candidate_id in chosen
        and item.candidate_id != lead
    ]
    return PortfolioSelection(
        population_id=population.population_id,
        lead_id=lead,
        alternative_ids=[item for item in chosen[1:] if item not in high_upside],
        high_upside_ids=high_upside,
        reasons={
            item: (
                (
                    "unscored candidate retained visibly; ranking is unavailable until an independent score succeeds"
                    if item not in score_by_id
                    else (
                        "LLM-identified high-upside Wildcard retained for human comparison; current maturity remains explicit"
                        if score_by_id[item].wildcard_recommended
                        else "quality-diversity portfolio selection based on the three formal scientific dimensions"
                    )
                )
            )
            for item in chosen
        },
    )


def _core_mean(report: ScoreReport) -> float:
    """Return a derived scalar only for deterministic tie-breaking.

    The three formal dimensions remain separately available to Pareto ranking.
    This mean never incorporates evidence, validation, uncertainty, Profile
    Fit, or upside diagnostics.
    """

    scores = report.scores
    return round(
        (scores.research_value + scores.mechanism_integrity + scores.contribution_distinctiveness) / 3,
        6,
    )


def _core_vector(report: ScoreReport) -> tuple[float, float, float]:
    scores = report.scores
    return (scores.research_value, scores.mechanism_integrity, scores.contribution_distinctiveness)


def _pareto_layers(candidate_ids: list[str], score_by_id: dict[str, ScoreReport]) -> list[list[str]]:
    """Return deterministic non-dominated layers over the three core scores."""

    remaining = set(candidate_ids)
    layers: list[list[str]] = []
    while remaining:
        non_dominated = [
            candidate_id
            for candidate_id in remaining
            if not any(
                _dominates(score_by_id[other], score_by_id[candidate_id])
                for other in remaining
                if other != candidate_id
            )
        ]
        if not non_dominated:  # Defensive only; finite strict comparisons always progress.
            non_dominated = list(remaining)
        layer = sorted(non_dominated, key=lambda item: (-_core_mean(score_by_id[item]), item))
        layers.append(layer)
        remaining.difference_update(layer)
    return layers


def _dominates(left: ScoreReport, right: ScoreReport) -> bool:
    left_vector = _core_vector(left)
    right_vector = _core_vector(right)
    return all(left_value >= right_value for left_value, right_value in zip(left_vector, right_vector)) and any(
        left_value > right_value for left_value, right_value in zip(left_vector, right_vector)
    )


def _flatten_layers(layers: list[list[str]]) -> list[str]:
    return [candidate_id for layer in layers for candidate_id in layer]


def _family_for_candidate(candidate_id: str, families: list[IdeaFamily]) -> str:
    for family in families:
        if candidate_id in family.member_ids:
            return family.family_id
    return candidate_id


def _survival_choice_priority(
    candidate_id: str,
    *,
    score_by_id: dict[str, ScoreReport],
    dossiers_by_id: dict[str, CandidateDossier],
    selected_ids: list[str],
    delta_by_id: dict[str, GeneDelta],
) -> tuple[float, float, float, int, int, str]:
    """Choose within one Pareto layer using non-score evolution metadata.

    Scientific scores are first. Parent--Child improvement and structural
    diversity only resolve candidates already in the same Pareto layer; the
    qualitative wildcard flags retain a distinct route to human comparison.
    """

    report = score_by_id[candidate_id]
    child_gain = 0.0
    delta = delta_by_id.get(candidate_id)
    if delta is not None:
        parent_reports = [score_by_id[parent_id] for parent_id in delta.parent_ids if parent_id in score_by_id]
        if parent_reports:
            parent = max(parent_reports, key=_core_mean)
            child_gain = max(
                current - previous
                for current, previous in zip(_core_vector(report), _core_vector(parent))
            )
    candidate = dossiers_by_id[candidate_id]
    if not selected_ids:
        diversity = 1.0
    else:
        diversity = min(
            1.0 - genome_similarity(candidate.genome, dossiers_by_id[selected].genome)
            for selected in selected_ids
            if selected in dossiers_by_id
        )
    return (
        _core_mean(report),
        round(child_gain, 6),
        round(diversity, 6),
        int(report.wildcard_recommended),
        int(report.high_upside),
        candidate_id,
    )


def _parent_priority(report: ScoreReport) -> tuple[float, int, int]:
    return (
        _core_mean(report),
        int(report.wildcard_recommended),
        int(report.high_upside),
    )


def _portfolio_priority(report: ScoreReport) -> float:
    return _core_mean(report)


def _survival_priority(report: ScoreReport) -> float:
    """Compatibility helper for callers that need a scalar core-score view."""

    return _core_mean(report)


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", value.casefold())
        if token not in {"the", "and", "for", "with", "that", "this", "method", "model", "paper"}
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _text(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    return " ".join(str(value or "").split())


def _genome_text(genome: IdeaGenome) -> str:
    return " ".join(_text(getattr(genome, name).value) for name in GENE_NAMES)


def _new_list_items(value: object, parent_values: Iterable[object]) -> list[str]:
    current = _text(value)
    parent_text = " ".join(_text(item) for item in parent_values)
    return sorted(token for token in _tokens(current) if token not in _tokens(parent_text))[:12]
