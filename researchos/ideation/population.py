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
    """Choose family-covered elite/repairable/high-upside parents, never top-K only."""

    score_by_id = {report.candidate_id: report for report in scores}
    dossier_by_id = {item.candidate_id: item for item in dossiers}
    selected: list[str] = []
    reasons: dict[str, str] = {}
    for family in families:
        ranked = sorted(
            (candidate_id for candidate_id in family.member_ids if candidate_id in score_by_id and candidate_id in dossier_by_id),
            key=lambda candidate_id: _parent_priority(score_by_id[candidate_id], profile_weight=profile_weight),
            reverse=True,
        )
        if not ranked or len(selected) >= maximum:
            continue
        choice = ranked[0]
        selected.append(choice)
        score = score_by_id[choice]
        reasons[choice] = "high_upside" if score.high_upside else "family_representative"
    for candidate_id, report in sorted(
        score_by_id.items(),
        key=lambda item: _parent_priority(item[1], profile_weight=profile_weight),
        reverse=True,
    ):
        if len(selected) >= maximum:
            break
        if candidate_id in selected or candidate_id not in dossier_by_id:
            continue
        selected.append(candidate_id)
        reasons[candidate_id] = "elite" if report.overall_readiness >= 4 else "repairable"
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
        decision_hint="reject_inflation" if growth == "high" else "acceptable",
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
    """Perform contract-first family survival and quality/diversity selection."""

    score_by_id = {item.candidate_id: item for item in scores}
    contract_by_id = {item.candidate_id: item for item in contracts}
    delta_by_id = {item.child_id: item for item in deltas}
    complexity_by_id = {item.candidate_id: item for item in complexity_reports}
    viable = [
        item.candidate_id for item in dossiers
        if item.candidate_id in score_by_id
        and contract_by_id.get(item.candidate_id, IdeaContractResult(candidate_id=item.candidate_id, status="fail", contracts={})).status != "fail"
        and complexity_by_id.get(item.candidate_id, ComplexityReport(candidate_id=item.candidate_id, complexity_growth="low")).decision_hint != "reject_inflation"
        and delta_by_id.get(item.candidate_id, GeneDelta(child_id=item.candidate_id, parent_ids=[item.candidate_id], classification="substantive", word_count_growth_ratio=1)).classification not in {"cosmetic", "regressive"}
    ]
    selected: list[str] = []
    decisions: dict[str, str] = {}
    for family in families:
        candidates = [candidate_id for candidate_id in family.member_ids if candidate_id in viable]
        if not candidates:
            continue
        winner = max(candidates, key=lambda candidate_id: score_by_id[candidate_id].overall_readiness)
        selected.append(winner)
        decisions[winner] = "family_survivor"
    remaining = sorted(
        (candidate_id for candidate_id in viable if candidate_id not in selected),
        key=lambda candidate_id: score_by_id[candidate_id].overall_readiness,
        reverse=True,
    )
    selected.extend(remaining[: max(0, target_size - len(selected))])
    selected = selected[:target_size]
    for candidate_id in selected:
        decisions.setdefault(candidate_id, "quality_diversity_survivor")
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
    weight = min(0.5, max(0.0, float(profile_weight)))
    ranked = sorted(
        population.active_candidate_ids,
        key=lambda candidate_id: _portfolio_priority(score_by_id[candidate_id], profile_weight=weight),
        reverse=True,
    )
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
    if not chosen and ranked:
        chosen.append(ranked[0])
    lead = chosen[0] if chosen else None
    high_upside = [item.candidate_id for item in scores if item.high_upside and item.candidate_id in chosen and item.candidate_id != lead]
    return PortfolioSelection(
        population_id=population.population_id,
        lead_id=lead,
        alternative_ids=[item for item in chosen[1:] if item not in high_upside],
        high_upside_ids=high_upside,
        reasons={
            item: (
                "quality-diversity portfolio selection; Profile Fit used as a secondary tie-breaker"
                if weight
                else "quality-diversity portfolio selection"
            )
            for item in chosen
        },
    )


def _parent_priority(report: ScoreReport, *, profile_weight: float = 0.0) -> tuple[float, int, float]:
    return (_portfolio_priority(report, profile_weight=profile_weight), int(report.high_upside), -report.score_uncertainty)


def _portfolio_priority(report: ScoreReport, *, profile_weight: float) -> float:
    weight = min(0.5, max(0.0, float(profile_weight)))
    return round(report.overall_readiness * (1 - weight) + report.profile_fit.overall_fit * weight, 6)


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
