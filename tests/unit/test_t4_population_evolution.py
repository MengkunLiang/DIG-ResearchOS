from __future__ import annotations

from researchos.ideation.models import (
    CandidateDossier,
    CandidateLineage,
    CandidateMaturity,
    CandidateStatus,
    Contribution,
    CrossoverCompatibilityDecision,
    EvolutionOperator,
    GeneDonorMap,
    IdeaGene,
    IdeaGenome,
    PopulationSnapshot,
    ProvisionalHypothesis,
    ScoreDimensions,
    ScoreReport,
)
from researchos.ideation.population import (
    build_idea_families,
    compile_crossover_plans,
    compile_mutation_plans,
    compute_gene_delta,
    detect_complexity_inflation,
    select_evolution_parents,
    select_portfolio,
    select_survivors,
    validate_idea_contract,
)


FINGERPRINT = "a" * 64


def _genome(candidate_id: str, *, problem: str, mechanism: str, contribution: str) -> IdeaGenome:
    gene = lambda value: IdeaGene(value=value)
    return IdeaGenome(
        candidate_id=candidate_id,
        route="fixture_route",
        problem=gene(problem),
        opportunity=gene("An unresolved condition needs an explanation."),
        challenged_assumption=gene("The current average-effect assumption may not hold."),
        core_thesis=gene("A coherent mechanism can be tested under the target condition."),
        mechanism=gene(mechanism),
        design_or_artifact=gene("A bounded intervention artifact."),
        contribution_package=gene(contribution),
        hypothesis_bundle=gene("Two falsifiable predictions."),
        validation_logic=gene("Compare the active mechanism with a disabling control."),
        boundary_conditions=gene("The effect should weaken outside the target condition."),
        risks=gene("A non-specific control effect would invalidate the mechanism."),
    )


def _dossier(candidate_id: str, *, problem: str, mechanism: str, contribution: str, parent_ids=None) -> CandidateDossier:
    return CandidateDossier(
        candidate_id=candidate_id,
        version=1,
        status=CandidateStatus.ACTIVE,
        maturity=CandidateMaturity.EVOLVED,
        genome=_genome(candidate_id, problem=problem, mechanism=mechanism, contribution=contribution),
        contributions=[
            Contribution(
                contribution_id=f"{candidate_id}-C1",
                statement="Make the mechanism boundary testable.",
                contribution_type="mechanism",
                what_changes_if_true="Future work must test the boundary rather than only report an average result.",
            ),
            Contribution(
                contribution_id=f"{candidate_id}-C2",
                statement="Provide a bounded validation design.",
                contribution_type="design",
                what_changes_if_true="Validation can reject an artifact explanation.",
            ),
        ],
        hypotheses=[
            ProvisionalHypothesis(
                hypothesis_id=f"{candidate_id}-H1",
                statement="The target condition changes the expected outcome.",
                mechanism="The stated mechanism responds to the target condition.",
                observable_prediction="The target group changes relative to the control.",
                discriminating_test="Disable the mechanism while holding the condition fixed.",
            ),
            ProvisionalHypothesis(
                hypothesis_id=f"{candidate_id}-H2",
                statement="The effect weakens outside the target condition.",
                mechanism="The boundary limits where the mechanism applies.",
                observable_prediction="The non-target group shows a smaller effect.",
                discriminating_test="Compare matched target and non-target groups.",
            ),
        ],
        lineage=CandidateLineage(
            candidate_id=candidate_id,
            parent_ids=parent_ids or [],
            route="fixture_route",
            created_by="evolver" if parent_ids else "generator",
        ),
    )


def _score(candidate_id: str, readiness: float, *, high_upside: bool = False) -> ScoreReport:
    return ScoreReport(
        candidate_id=candidate_id,
        scoring_batch_id="SB1",
        scores=ScoreDimensions(
            research_value=readiness,
            mechanism_integrity=readiness,
            contribution_distinctiveness=readiness,
            evidence_calibration=readiness,
            validation_tractability=readiness,
        ),
        overall_readiness=readiness,
        score_uncertainty=0.2,
        rationales={
            "research_value": "reason",
            "mechanism_integrity": "reason",
            "contribution_distinctiveness": "reason",
            "evidence_calibration": "reason",
            "validation_tractability": "reason",
        },
        dominant_strength="A coherent, bounded mechanism.",
        dominant_bottleneck="The validation needs a stronger control.",
        preserve_genes=["problem"],
        modify_genes=["validation_logic"],
        recommended_operators=[EvolutionOperator.REPAIR_VALIDATION],
        high_upside=high_upside,
    )


def test_population_operations_preserve_parent_lineage_and_make_p1_selection():
    parent_a = _dossier("I1", problem="Condition-dependent response problem", mechanism="Conditioned response mechanism", contribution="Mechanism contribution")
    parent_b = _dossier("I2", problem="Condition-dependent response problem", mechanism="Alternative response mechanism", contribution="Evaluation contribution")
    parent_c = _dossier("I3", problem="Separate measurement problem", mechanism="Measurement mechanism", contribution="Measurement contribution")
    child = _dossier("I4", problem="Condition-dependent response problem", mechanism="Conditioned response mechanism with a disabling control", contribution="Mechanism contribution with control", parent_ids=["I1"])
    dossiers = [parent_a, parent_b, parent_c, child]
    families = build_idea_families([item.genome for item in dossiers], generation=0, similarity_threshold=0.35)
    scores = [_score("I1", 3.7), _score("I2", 3.8, high_upside=True), _score("I3", 3.6), _score("I4", 4.2)]

    parent_ids, reasons = select_evolution_parents(dossiers[:3], scores[:3], families, maximum=3)
    assert parent_ids
    assert reasons
    mutation_plans = compile_mutation_plans(parent_ids, scores[:3], round_number=1, limit=2)
    assert 1 <= len(mutation_plans) <= 2
    assert all(plan.child_type == "mutation" for plan in mutation_plans)

    crossover_plans = compile_crossover_plans(
        [
            CrossoverCompatibilityDecision(
                pair_id="I1__I2",
                parent_ids=["I1", "I2"],
                decision="approved",
                problem_compatibility="Both address the same bounded problem.",
                bottleneck_complementarity="One supplies a mechanism and the other a validation constraint.",
                mechanism_coherence="The donor map keeps a single mechanism chain.",
                proposed_gene_donor_map=GeneDonorMap(donors={"mechanism": "I1", "validation_logic": "I2"}),
            )
        ],
        round_number=1,
        limit=2,
    )
    assert len(crossover_plans) == 1
    assert crossover_plans[0].gene_donor_map is not None

    child_plan = compile_mutation_plans(["I1"], scores[:3], round_number=1, limit=1)[0]
    delta = compute_gene_delta(child, [parent_a], child_plan)
    assert delta.child_id == "I4"
    assert "problem" not in delta.violated_preserve_constraints
    complexity = detect_complexity_inflation(child, [parent_a], ratio_limit=2.0)
    assert complexity.decision_hint == "acceptable"
    contracts = [validate_idea_contract(item) for item in dossiers]
    assert all(contract.status == "pass" for contract in contracts)
    survivor_ids, archived_ids, decisions = select_survivors(
        dossiers,
        scores,
        contracts,
        [delta],
        [complexity],
        families,
        target_size=3,
    )
    assert "I4" in survivor_ids
    assert set(survivor_ids).isdisjoint(archived_ids)
    assert decisions["I4"] in {"family_survivor", "quality_diversity_survivor"}
    p1 = PopulationSnapshot(
        population_id="P1",
        generation=1,
        input_fingerprint=FINGERPRINT,
        run_config_fingerprint=FINGERPRINT,
        active_candidate_ids=survivor_ids,
        archived_candidate_ids=archived_ids,
    )
    portfolio = select_portfolio(p1, scores, families, maximum=3)
    assert portfolio.lead_id in survivor_ids
    assert len([portfolio.lead_id, *portfolio.alternative_ids, *portfolio.high_upside_ids]) <= 3


def test_contract_and_survival_reject_cosmetic_or_complexity_inflated_child():
    parent = _dossier("I1", problem="Problem", mechanism="Mechanism", contribution="Contribution")
    child = _dossier("I2", problem="Problem", mechanism="Mechanism", contribution="Contribution", parent_ids=["I1"])
    family = build_idea_families([parent.genome, child.genome], generation=0, similarity_threshold=0.2)
    scores = [_score("I1", 3.5), _score("I2", 4.5)]
    plan = compile_mutation_plans(["I1"], [scores[0]], round_number=1, limit=1)[0]
    delta = compute_gene_delta(child, [parent], plan)
    assert delta.classification == "cosmetic"
    contracts = [validate_idea_contract(parent), validate_idea_contract(child)]
    complexity = detect_complexity_inflation(child, [parent], ratio_limit=1.01)
    survivors, archived, _ = select_survivors(
        [parent, child], scores, contracts, [delta], [complexity], family, target_size=2
    )
    assert "I2" in archived
    assert "I1" in survivors
