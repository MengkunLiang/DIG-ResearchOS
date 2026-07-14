from __future__ import annotations

import json

import pytest

from researchos.ideation.config import OffspringDefaults, PopulationDefaults, RouteQuota, T4EvolutionSettings
from researchos.ideation.evolution_controller import IdeaEvolutionController
from researchos.ideation.models import (
    CandidateDossier,
    CandidateLineage,
    CandidateMaturity,
    CandidateStatus,
    Contribution,
    EvolutionOperator,
    IdeaGene,
    IdeaGenome,
    OpportunityQuery,
    ProvisionalHypothesis,
    ScoreDimensions,
    ScoreReport,
    T4RunConfig,
)


def _write_inputs(workspace):
    (workspace / "literature" / "paper_notes").mkdir(parents=True)
    (workspace / "user_seeds").mkdir()
    (workspace / "project.yaml").write_text("project_id: test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("synthesis\n", encoding="utf-8")
    (workspace / "literature" / "synthesis_workbench.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "domain_map.json").write_text("{}\n", encoding="utf-8")
    (workspace / "literature" / "comparison_table.csv").write_text("id,title\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_ideas.md").write_text("\n", encoding="utf-8")
    (workspace / "user_seeds" / "seed_constraints.md").write_text("\n", encoding="utf-8")
    (workspace / "literature" / "paper_notes" / "p1.md").write_text(
        "# Note\n\n## Mechanism\n\nBounded evidence for a fixture mechanism.\n", encoding="utf-8"
    )


def _candidate(candidate_id: str, route: str, *, parent_ids=None, mechanism="Fixture mechanism"):
    gene = lambda value: IdeaGene(value=value)
    return CandidateDossier(
        candidate_id=candidate_id,
        version=1,
        status=CandidateStatus.ACTIVE,
        maturity=CandidateMaturity.EVOLVED,
        genome=IdeaGenome(
            candidate_id=candidate_id,
            route=route,
            problem=gene("A bounded fixture problem."),
            opportunity=gene("A testable fixture opportunity."),
            challenged_assumption=gene("The baseline assumption can fail."),
            core_thesis=gene("A coherent mechanism changes the expected outcome."),
            mechanism=gene(mechanism),
            design_or_artifact=gene("A bounded fixture artifact."),
            contribution_package=gene("A mechanism and validation contribution."),
            hypothesis_bundle=gene("Two falsifiable fixture hypotheses."),
            validation_logic=gene("Use an active and disabling control."),
            boundary_conditions=gene("The effect weakens outside the target condition."),
            risks=gene("A non-specific control effect rejects the claim."),
        ),
        contributions=[
            Contribution(
                contribution_id=f"{candidate_id}-C1",
                statement="Make a bounded mechanism testable.",
                contribution_type="mechanism",
                what_changes_if_true="Future work must evaluate the stated boundary.",
            ),
            Contribution(
                contribution_id=f"{candidate_id}-C2",
                statement="Add a discriminating control.",
                contribution_type="design",
                what_changes_if_true="Validation can reject a non-specific explanation.",
            ),
        ],
        hypotheses=[
            ProvisionalHypothesis(
                hypothesis_id=f"{candidate_id}-H1",
                statement="The target condition changes the outcome.",
                mechanism="The stated mechanism reacts to the target condition.",
                observable_prediction="The target group differs from control.",
                discriminating_test="Disable the mechanism under the same condition.",
            ),
            ProvisionalHypothesis(
                hypothesis_id=f"{candidate_id}-H2",
                statement="The effect weakens outside the condition.",
                mechanism="The mechanism has a boundary.",
                observable_prediction="The non-target group has a smaller effect.",
                discriminating_test="Compare matched target and non-target groups.",
            ),
        ],
        lineage=CandidateLineage(
            candidate_id=candidate_id,
            parent_ids=parent_ids or [],
            route=route,
            created_by="evolver" if parent_ids else "generator",
        ),
    )


class FakeGenerator:
    async def plan_opportunities(self, *, evidence_summary, run_config):
        return [
            OpportunityQuery(
                opportunity_id=f"O{index}",
                type="mechanism_gap",
                one_line_summary=f"Fixture opportunity {index}",
                question="Which bounded mechanism is testable?",
                why_it_matters="The fixture requires a falsifiable explanation.",
                compatible_routes=["evidence_routed_literature", "informed_brainstorm"],
            )
            for index in range(1, 4)
        ]

    async def generate_route(self, *, route, opportunities, evidence_bundle, quota, repair):
        suffix = "L" if route == "evidence_routed_literature" else "B"
        return [_candidate(f"I{suffix}{index}", route, mechanism=f"{suffix} mechanism {index}") for index in range(1, quota + 1)]


class FakeScorer:
    async def score_population(self, *, candidates, scoring_batch_id, blind):
        reports = []
        for index, candidate in enumerate(candidates, start=1):
            readiness = 3.0 + index / 10
            reports.append(
                ScoreReport(
                    candidate_id=candidate.candidate_id,
                    scoring_batch_id=scoring_batch_id,
                    blind=blind,
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
                    dominant_strength="A bounded mechanism.",
                    dominant_bottleneck="The validation needs a control.",
                    preserve_genes=["problem"],
                    modify_genes=["validation_logic"],
                    recommended_operators=[EvolutionOperator.REPAIR_VALIDATION],
                )
            )
        return reports

    async def review_crossover_pairs(self, *, candidates, pairs):
        return []


class FakeEvolver:
    async def generate_offspring(self, *, plans, parents):
        by_id = {item.candidate_id: item for item in parents}
        children = []
        for index, plan in enumerate(plans, start=1):
            parent = by_id[plan.parent_ids[0]]
            children.append(
                _candidate(
                    f"M{plan.round}-{index}",
                    parent.genome.route,
                    parent_ids=plan.parent_ids,
                    mechanism=parent.genome.mechanism.value + " with a disabling control",
                )
            )
        return children


def _settings():
    return T4EvolutionSettings(
        route_quotas=[
            RouteQuota(route="evidence_routed_literature", minimum=1, maximum=1, required=True),
            RouteQuota(route="informed_brainstorm", minimum=1, maximum=1, required=True),
        ],
        population=PopulationDefaults(
            max_initial_population=6,
            active_population_target=3,
            active_population_minimum=1,
            active_population_maximum=3,
        ),
        offspring=OffspringDefaults(
            mutation_minimum=1,
            mutation_maximum=1,
            crossover_minimum=0,
            crossover_maximum=0,
            max_total=1,
        ),
    )


@pytest.mark.asyncio
async def test_controller_completes_default_p0_to_p1_with_role_separation(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    result = await controller.run(
        T4RunConfig(
            mode="standard",
            rounds=1,
            allow_crossover=False,
            final_top_k=2,
            max_initial_population=6,
            active_population_size=3,
            max_offspring_per_round=1,
            max_crossover_children=0,
            route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
        )
    )
    assert result.population.population_id == "P1"
    assert result.state.current_population_id == "P1"
    assert result.state.completed_rounds == 1
    assert len(result.route_results) == 2
    assert (tmp_path / "ideation" / "populations" / "P0.json").exists()
    assert (tmp_path / "ideation" / "populations" / "P1.json").exists()
    assert (tmp_path / "ideation" / "evolution" / "round_1.json").exists()
    assert (tmp_path / "ideation" / "portfolio.json").exists()
    diagnostics = json.loads((tmp_path / "ideation" / "evolution" / "round_1_diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["gene_deltas"]
    assert diagnostics["contracts"]


@pytest.mark.asyncio
async def test_controller_reuses_completed_p0_without_regenerating_routes(tmp_path):
    _write_inputs(tmp_path)
    generator = FakeGenerator()
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=generator,
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    config = T4RunConfig(
        mode="quick",
        rounds=0,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=0,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    second = await controller.run(config)
    assert first.population.population_id == second.population.population_id == "P0"
    assert second.state.completed_rounds == 0


@pytest.mark.asyncio
async def test_controller_reuses_completed_p1_without_another_evolution_round(tmp_path):
    _write_inputs(tmp_path)
    events = []

    async def progress(phase, status, payload):
        events.append((phase.value, status, payload))

    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
        progress_callback=progress,
    )
    config = T4RunConfig(
        mode="standard",
        rounds=1,
        allow_crossover=False,
        final_top_k=2,
        max_initial_population=6,
        active_population_size=3,
        max_offspring_per_round=1,
        max_crossover_children=0,
        route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
    )
    first = await controller.run(config)
    second = await controller.run(config)

    assert first.population.population_id == second.population.population_id == "P1"
    assert second.active_dossiers
    assert {item.candidate_id for item in second.active_scores} == set(second.population.active_candidate_ids)
    assert any(status == "reused" for _phase, status, _payload in events)


@pytest.mark.asyncio
async def test_deep_mode_completes_a_second_population_update(tmp_path):
    _write_inputs(tmp_path)
    controller = IdeaEvolutionController(
        workspace_dir=tmp_path,
        settings=_settings(),
        generator=FakeGenerator(),
        scorer=FakeScorer(),
        evolver=FakeEvolver(),
    )
    result = await controller.run(
        T4RunConfig(
            mode="deep",
            rounds=2,
            allow_crossover=False,
            final_top_k=2,
            max_initial_population=6,
            active_population_size=3,
            max_offspring_per_round=1,
            max_crossover_children=0,
            route_quotas={"evidence_routed_literature": 1, "informed_brainstorm": 1},
        )
    )

    assert result.population.population_id == "P2"
    assert result.state.completed_rounds == 2
    assert (tmp_path / "ideation" / "populations" / "P2.json").exists()
    assert (tmp_path / "ideation" / "evolution" / "round_2.json").exists()
    assert (tmp_path / "ideation" / "evolution" / "round_2_diagnostics.json").exists()
